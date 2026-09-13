"""把游戏兼容性报告发布为 GitHub Issue，图片证据以 QQ CDN 链接随 Issue 附上。"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

import httpx

from .compatibility_store import STATUS_LABELS
from .config import CompatibilityConfig

if TYPE_CHECKING:
    from .compatibility_store import CompatibilityReport

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_LONG_NUMBER_RE = re.compile(r"(?<!\d)\d{5,}(?!\d)")


class CompatibilityIssuePublisher:
    """向兼容性仓库创建 Issue；令牌优先级：compatibility.token > 环境变量 > github_issues.token。"""

    def __init__(self, config: CompatibilityConfig, fallback_token: str = "") -> None:
        self._config = config
        self._fallback_token = fallback_token.strip()

    async def submit(self, report: "CompatibilityReport") -> tuple[bool, int | None, str, str]:
        """返回 (是否成功, Issue 编号, Issue URL 或为空, 错误信息或为空)。"""
        if not self._config.enabled:
            return False, None, "", "兼容性自动提交已关闭"
        if not self._config.repository or "/" not in self._config.repository:
            return False, None, "", "兼容性仓库配置无效"
        token = self._resolve_token()
        if not token:
            return False, None, "", "未配置 GitHub 令牌（compatibility.token 或环境变量）"

        status_label = STATUS_LABELS.get(report.status, report.status)
        game_label = report.game or "未知游戏"
        endpoint = (
            f"{self._config.api_base_url.rstrip('/')}/repos/"
            f"{self._config.repository}/issues"
        )
        payload = {
            "title": f"[兼容性][{status_label}] {game_label} - {report.summary[:90]}",
            "body": self._build_issue_body(report),
            "labels": ["兼容性", status_label],
        }
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            async with httpx.AsyncClient(timeout=self._config.timeout) as client:
                response = await client.post(endpoint, headers=headers, json=payload)
        except Exception as exc:
            return False, None, "", f"GitHub 请求失败：{exc}"
        if response.status_code != 201:
            return False, None, "", (
                f"GitHub 创建 Issue 失败（HTTP {response.status_code}）："
                f"{response.text[:240]}"
            )
        try:
            data = response.json()
            number = int(data.get("number"))
            url = str(data.get("html_url", ""))
        except (TypeError, ValueError):
            return False, None, "", "GitHub 返回的 Issue 数据无效"
        return True, number, url, ""

    def _resolve_token(self) -> str:
        # 令牌不会写入日志或任何对外返回内容。
        return (
            self._config.token.strip()
            or os.environ.get(self._config.token_env, "").strip()
            or self._fallback_token
        )

    def _build_issue_body(self, report: "CompatibilityReport") -> str:
        rows = [
            "## 机器人整理",
            f"- 结论：{report.status_label}",
            f"- 摘要：{report.summary}",
            "",
            "## 游戏信息",
            f"- 游戏：{report.game or '未知（报告者未说明）'}",
            f"- 游戏版本：{report.game_version or '不明'}",
            f"- 模拟器/运行方式：{report.emulator or '不明'}",
        ]
        if report.driver:
            rows.append(f"- 驱动/转译器：{report.driver}")
        if report.settings:
            rows.append(f"- 关键设置：{report.settings}")
        rows.extend(
            [
                "",
                "## 运行环境",
                f"- 设备：{report.device or '不明'}",
                f"- 系统版本：{report.system_version or '不明'}",
                f"- 小柚 App 版本：{report.app_version or '最新版（报告者未提供，默认按最新版记录）'}",
            ]
        )
        if self._config.include_reporter_name:
            rows.extend(
                [
                    "",
                    "## 报告者",
                    f"- 群昵称：{self._safe_inline(report.reporter_name) if report.reporter_name else '（未提供）'}",
                    f"- 群组/时间：{report.group_id or '私聊'} / {report.created_at}",
                ]
            )
        if report.extra_text:
            rows.extend(
                [
                    "",
                    "## 报告者补充说明",
                    "> " + self._redact(report.extra_text).replace("\n", "\n> "),
                ]
            )
        rows.extend(
            [
                "",
                "## 用户原始描述（已脱敏）",
                "> " + self._redact(report.source_text).replace("\n", "\n> "),
            ]
        )
        rows.extend(["", "## 附件"])
        if self._config.include_media_links:
            image_index = 0
            video_index = 0
            attached = False
            for media in report.media:
                source_url = str(media.get("source_url", "")).strip()
                if not source_url.startswith(("http://", "https://")):
                    continue
                attached = True
                safe_url = source_url.replace(">", "%3E")
                if media.get("kind") == "image":
                    image_index += 1
                    rows.append(f"![用户图片 {image_index}](<{safe_url}>)")
                elif media.get("kind") == "video":
                    video_index += 1
                    rows.append(f"- [用户视频 {video_index}](<{safe_url}>)")
            if attached:
                rows.append("- 图片/视频通过 QQ CDN 链接提供，链接可能过期；机器人本地仍保留原始文件。")
            else:
                rows.append("- 本条报告未附带图片/视频。")
        else:
            saved = sum(1 for m in report.media if m.get("status") == "saved")
            rows.append(f"- 附件仅保存在机器人工作目录（本地已保存 {saved} 个），未同步外链。")
        return "\n".join(rows)

    @staticmethod
    def _safe_inline(value: str) -> str:
        return re.sub(r"[\r\n]+", " ", value).strip()[:80]

    @staticmethod
    def _redact(value: str) -> str:
        compact = _EMAIL_RE.sub("[已隐藏邮箱]", value.strip())
        compact = _LONG_NUMBER_RE.sub("[已隐藏编号]", compact)
        return compact[:1600] or "（用户未提供文字描述）"
